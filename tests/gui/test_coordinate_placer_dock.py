# tests/gui/test_coordinate_placer_dock.py
"""
CoordinatePlacerDock tests are deliberately headless AND board-mutation-free
— same reasoning as tests/gui/test_thermal_via_dock.py: _on_place()'s real
job is moving real footprints on a live board, which these tests must never
do on their own. ApplyPipeline/load_config are monkeypatched with fakes
that only check what CoordinatePlacerDock PASSES them.
"""
import yaml
from types import SimpleNamespace

import gui.docks.coordinate_placer as coordinate_placer_mod
from gui.docks.coordinate_placer import CoordinatePlacerDock
from kicadstamp.config import Config, RuntimeContext


def _write_yaml(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _make_dock(main_window, tmp_path):
    target_file = tmp_path / "root.yaml"
    _write_yaml(target_file, {"coordinate_placements": []})
    dock = CoordinatePlacerDock(main_window)
    dock.set_target_file(target_file)
    return dock, target_file


def _fill_cartesian_row(dock, row, cluster="FPGA_PERIPH", role="R18",
                        x=10.0, y=20.0, rotation=0.0):
    dock.table.cellWidget(row, coordinate_placer_mod._COL_CLUSTER).setCurrentText(cluster)
    dock.table.cellWidget(row, coordinate_placer_mod._COL_ROLE).setCurrentText(role)
    dock.table.cellWidget(row, coordinate_placer_mod._COL_X).setText(str(x))
    dock.table.cellWidget(row, coordinate_placer_mod._COL_Y).setText(str(y))
    dock.table.cellWidget(row, coordinate_placer_mod._COL_ROTATION).setText(str(rotation))


# ── Row widgets / mode & anchor toggling ────────────────────────────────────

def test_add_row_defaults_to_cartesian_and_center(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()

    assert dock.table.rowCount() == 1
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_MODE).currentIndex() == 0
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_ANCHOR).currentIndex() == 0
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_X).isEnabled() is True
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_CENTER_X).isEnabled() is False
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_PAD).isEnabled() is False


def test_switching_to_polar_mode_toggles_enabled_cells(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()

    dock.table.cellWidget(0, coordinate_placer_mod._COL_MODE).setCurrentIndex(1)  # Polar

    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_X).isEnabled() is False
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_Y).isEnabled() is False
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_CENTER_X).isEnabled() is True
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_RADIUS).isEnabled() is True
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_ANGLE).isEnabled() is True


def test_switching_to_pad_anchor_enables_pad_cell(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()

    dock.table.cellWidget(0, coordinate_placer_mod._COL_ANCHOR).setCurrentIndex(1)  # Pad

    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_PAD).isEnabled() is True


def test_add_row_from_entry_populates_polar_and_pad_fields(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row({
        "cluster": "X", "role": "R1", "center_x_mm": 1.0, "center_y_mm": 2.0,
        "radius_mm": 3.0, "angle_deg": 45.0, "anchor": "pad", "anchor_pad": "2",
    })

    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_MODE).currentIndex() == 1
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_ANCHOR).currentIndex() == 1
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_CENTER_X).text() == "1.0"
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_PAD).text() == "2"


# ── Anchor-relative mode (2026-08-12, Group 0) ──────────────────────────────

def test_switching_to_anchor_mode_toggles_anchor_columns(main_window, tmp_path):
    """Anchor mode enables the anchor identity columns, uses the Pad column
    as the ANCHOR component's pad, and disables the self-referential
    "Anchor" (Center/Pad) column."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()

    dock.table.cellWidget(0, coordinate_placer_mod._COL_MODE).setCurrentIndex(2)  # Anchor

    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_ANCHOR).isEnabled() is False
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_ANCHOR_REF).isEnabled() is True
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_ANCHOR_ROLE).isEnabled() is True
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_ANCHOR_POINT).isEnabled() is True
    # X/Y are the OFFSET in anchor mode; the absolute polar fields stay off.
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_X).isEnabled() is True
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_CENTER_X).isEnabled() is False


def test_add_row_from_anchor_entry_sets_anchor_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row({
        "cluster": "X", "role": "R1", "x_mm": 10.0, "y_mm": -70.0,
        "anchor_point": "Origin", "rotation_deg": 270.0,
    })

    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_MODE).currentIndex() == 2
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_ANCHOR_POINT).text() == "Origin"
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_X).text() == "10.0"


def test_build_entries_anchor_mode_round_trips_through_loader(main_window, tmp_path):
    """An anchor-relative row (Mode=Anchor, X/Y offset, anchor_role + pad)
    must build a dict that the real backend loader accepts."""
    from kicadstamp.config import load_coordinate_placement

    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()
    dock.table.cellWidget(0, coordinate_placer_mod._COL_MODE).setCurrentIndex(2)  # Anchor
    dock.table.cellWidget(0, coordinate_placer_mod._COL_CLUSTER).setCurrentText("FPGA_PERIPH")
    dock.table.cellWidget(0, coordinate_placer_mod._COL_ROLE).setCurrentText("R18")
    dock.table.cellWidget(0, coordinate_placer_mod._COL_X).setText("2.0")
    dock.table.cellWidget(0, coordinate_placer_mod._COL_Y).setText("3.0")
    dock.table.cellWidget(0, coordinate_placer_mod._COL_ANCHOR_ROLE).setCurrentText("FPGA")
    # In anchor mode the Pad column means the ANCHOR component's pad.
    dock.table.cellWidget(0, coordinate_placer_mod._COL_PAD).setText("A17")

    result = dock._build_entries()
    assert result is not None
    entries, _placements = result
    assert len(entries) == 1
    cp = load_coordinate_placement(entries[0])  # must validate against the real loader
    assert cp.anchor_role == "FPGA"
    assert cp.anchor_pad == "A17"
    assert cp.x_mm == 2.0 and cp.y_mm == 3.0


def test_delete_selected_removes_the_row(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row({"cluster": "X", "role": "R1", "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 0.0})
    dock._add_row({"cluster": "X", "role": "R2", "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 0.0})
    dock.table.selectRow(0)

    dock._on_delete_selected()

    assert dock.table.rowCount() == 1
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_ROLE).currentText() == "R2"


def test_mode_toggle_after_row_deletion_targets_the_right_row(main_window, tmp_path):
    """2026-08-12, Group 2 fix: the Mode/Anchor handlers captured the row index
    at _add_row() time — deleting a row above shifted the survivors, so toggling
    Mode on a survivor either crashed (stale index out of range) or silently
    changed the WRONG row. The index is now resolved from the widget at signal
    time."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row({"cluster": "X", "role": "R1", "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 0.0})
    dock._add_row({"cluster": "X", "role": "R2", "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 0.0})

    dock.table.selectRow(0)
    dock._on_delete_selected()

    # Row 0 now holds what was R2. Switching its Mode must toggle ROW 0's
    # fields (not an out-of-range row 1, and not row 0 of the old indexing).
    dock.table.cellWidget(0, coordinate_placer_mod._COL_MODE).setCurrentIndex(1)  # Polar
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_X).isEnabled() is False
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_CENTER_X).isEnabled() is True


def test_collect_place_inputs_excludes_retired_and_skip_rows_from_only_names(main_window, tmp_path, monkeypatch):
    """2026-08-12, Group 2 fix: retired/skip rows used to be included in the
    --only names — drop_inactive_items dropped them before apply_only_filter,
    which then couldn't find the name and failed "Place all" wholesale."""
    import gui.docks.coordinate_placer as m

    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row({"cluster": "X", "role": "R1", "x_mm": 0.0, "y_mm": 0.0,
                   "rotation_deg": 0.0, "retired": True})
    dock._add_row({"cluster": "X", "role": "R2", "x_mm": 0.0, "y_mm": 0.0,
                   "rotation_deg": 0.0})

    monkeypatch.setattr(m, "load_config", lambda path: (Config(), RuntimeContext()))
    payload = dock._collect_place_inputs()

    assert payload is not None
    assert payload["names"] == ["X/R2"]


def test_delete_with_no_selection_shows_error(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row({"cluster": "X", "role": "R1", "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 0.0})

    dock._on_delete_selected()

    assert dock.table.rowCount() == 1
    assert "Select a row" in dock.message_label.text()


# ── Row <-> entry round trip / validation ───────────────────────────────────

def test_row_to_entry_cartesian(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()
    _fill_cartesian_row(dock, 0)

    entry = dock._row_to_entry(0)

    assert entry == {"cluster": "FPGA_PERIPH", "role": "R18", "x_mm": 10.0, "y_mm": 20.0,
                     "rotation_deg": 0.0}


def test_row_with_non_numeric_field_reports_row_and_column(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()
    _fill_cartesian_row(dock, 0)
    dock.table.cellWidget(0, coordinate_placer_mod._COL_X).setText("abc")

    entry = dock._row_to_entry(0)

    assert entry is None
    assert "Row 1" in dock.message_label.text()
    assert "'abc'" in dock.message_label.text()


def test_build_entries_surfaces_loader_validation_error_with_row_number(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()
    # Missing role -> load_coordinate_placement fatals with "missing cluster/role"
    dock.table.cellWidget(0, coordinate_placer_mod._COL_CLUSTER).setCurrentText("X")

    entries = dock._build_entries()

    assert entries is None
    # The label shows only the first line (see show_message's own
    # docstring — full multi-line FATAL ERROR blocks go to the Log dock,
    # not the inline label), so the row number is checked against the
    # label, the detailed validation text against the mirrored log record.
    assert "Row 1" in dock.message_label.text()
    assert "missing cluster/role" in caplog.text


def test_build_entries_detects_duplicate_default_names(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()
    _fill_cartesian_row(dock, 0, role="R18")
    dock._add_row()
    _fill_cartesian_row(dock, 1, role="R18")  # same cluster/role -> same default name

    entries = dock._build_entries()

    assert entries is None
    assert "Duplicate name" in dock.message_label.text()
    assert "FPGA_PERIPH/R18" in dock.message_label.text()


def test_build_entries_returns_all_rows_when_valid(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()
    _fill_cartesian_row(dock, 0, role="R18")
    dock._add_row()
    _fill_cartesian_row(dock, 1, role="R19")

    result = dock._build_entries()

    assert result is not None
    entries, _placements = result
    assert [e["role"] for e in entries] == ["R18", "R19"]


# ── Save ──────────────────────────────────────────────────────────────────

def test_save_writes_whole_table_via_set_list_section(main_window, tmp_path, monkeypatch):
    dock, target_file = _make_dock(main_window, tmp_path)
    dock._add_row()
    _fill_cartesian_row(dock, 0)

    captured = {}
    monkeypatch.setattr(coordinate_placer_mod, "set_list_section",
                        lambda path, section, entries: captured.update(
                            path=path, section=section, entries=entries))

    saved_signal = []
    dock.saved.connect(lambda: saved_signal.append(1))

    dock._on_save()

    assert captured["path"] == target_file
    assert captured["section"] == "coordinate_placements"
    assert captured["entries"] == [
        {"cluster": "FPGA_PERIPH", "role": "R18", "x_mm": 10.0, "y_mm": 20.0, "rotation_deg": 0.0}
    ]
    assert saved_signal == [1]
    assert "Wrote 1" in dock.message_label.text()


def test_save_without_target_file_shows_error(main_window, tmp_path):
    dock = CoordinatePlacerDock(main_window)
    dock._add_row()
    _fill_cartesian_row(dock, 0)

    dock._on_save()

    assert "Pick a file" in dock.message_label.text()


def test_save_does_not_write_when_a_row_is_invalid(main_window, tmp_path, monkeypatch):
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()  # blank row -> invalid (no cluster/role)

    called = []
    monkeypatch.setattr(coordinate_placer_mod, "set_list_section",
                        lambda *a, **kw: called.append(1))

    dock._on_save()

    assert called == []


# ── load_from_file ───────────────────────────────────────────────────────

def test_load_from_file_populates_rows(main_window, tmp_path):
    target_file = tmp_path / "root.yaml"
    _write_yaml(target_file, {"coordinate_placements": [
        {"cluster": "FPGA_PERIPH", "role": "R18", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0},
        {"cluster": "FPGA_PERIPH", "role": "R19", "center_x_mm": 0.0, "center_y_mm": 0.0,
         "radius_mm": 5.0, "angle_deg": 30.0},
    ]})
    dock = CoordinatePlacerDock(main_window)

    dock.load_from_file(target_file)

    assert dock.table.rowCount() == 2
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_ROLE).currentText() == "R18"
    assert dock.table.cellWidget(0, coordinate_placer_mod._COL_MODE).currentIndex() == 0
    assert dock.table.cellWidget(1, coordinate_placer_mod._COL_ROLE).currentText() == "R19"
    assert dock.table.cellWidget(1, coordinate_placer_mod._COL_MODE).currentIndex() == 1


def test_load_from_file_with_missing_file_leaves_table_empty(main_window, tmp_path):
    dock = CoordinatePlacerDock(main_window)
    missing = tmp_path / "does_not_exist.yaml"

    dock.load_from_file(missing)

    assert dock.table.rowCount() == 0
    assert dock._path == missing


def test_load_from_file_clears_previous_rows(main_window, tmp_path):
    target_file = tmp_path / "root.yaml"
    _write_yaml(target_file, {"coordinate_placements": [
        {"cluster": "X", "role": "R1", "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 0.0},
    ]})
    dock = CoordinatePlacerDock(main_window)
    dock._add_row()  # a stray manually-added row before loading
    dock._add_row()

    dock.load_from_file(target_file)

    assert dock.table.rowCount() == 1


# ── Place (async dispatch) ──────────────────────────────────────────────────

def test_on_place_dispatches_to_worker(main_window, tmp_path, monkeypatch):
    """The Place button must NOT block the UI thread: _on_place() collects
    + validates inputs on the UI thread (including loading the target
    config), then hands the plain-data payload to start_long_op."""
    dock, target_file = _make_dock(main_window, tmp_path)
    dock._add_row()
    _fill_cartesian_row(dock, 0)

    fake_cfg = Config()
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(coordinate_placer_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    captured = {}

    def _fake_start(connection, widgets, fn, on_success, on_error, *args):
        captured["connection"] = connection
        captured["widgets"] = widgets
        captured["args"] = args
        return "fake-controller"

    monkeypatch.setattr(coordinate_placer_mod, "start_long_op", _fake_start)

    dock._on_place()

    assert dock._active_op == "fake-controller"
    assert captured["connection"] is main_window.connection
    assert captured["widgets"] == (dock.save_button, dock.place_button)
    payload = captured["args"][0]
    assert payload["path"] == target_file
    assert payload["cfg"] is fake_cfg
    assert payload["ctx"] is fake_ctx
    assert payload["names"] == ["FPGA_PERIPH/R18"]
    assert len(fake_cfg.coordinate_placements) == 1
    assert fake_cfg.coordinate_placements[0].role == "R18"


def test_on_place_with_empty_table_shows_error(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)

    dock._on_place()

    assert "Nothing to place" in dock.message_label.text()


def test_on_place_replaces_only_this_tables_own_rows_by_name(main_window, tmp_path, monkeypatch):
    """Rows already in the file under names NOT present in this table's
    current content must survive untouched — replace-by-name-set, same
    spirit as ThermalViaArrayDock's own Redraw comment."""
    dock, target_file = _make_dock(main_window, tmp_path)
    dock._add_row()
    _fill_cartesian_row(dock, 0, role="R18")

    from kicadstamp.config import CoordinatePlacement
    fake_cfg = Config(coordinate_placements=[
        CoordinatePlacement(cluster="OTHER", role="R99", x_mm=0.0, y_mm=0.0, rotation_deg=0.0),
    ])
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(coordinate_placer_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    payload = dock._collect_place_inputs()

    roles = sorted(cp.role for cp in payload["cfg"].coordinate_placements)
    assert roles == ["R18", "R99"]


# ── refresh_known_roles — set-compare cache (2026-08-12, Group 4) ───────────

def test_refresh_known_roles_skips_repopulation_when_unchanged(main_window, tmp_path, monkeypatch):
    """G4.4 (2026-08-12): refresh_known_roles runs on the ~2s poll tick, so it
    must NOT repopulate every combo when the snapshot's Role/Cluster sets
    haven't changed — same set-compare guard as extract.py's
    _rebuild_net_aliases. A changed set DOES repopulate again."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock._add_row()
    calls = []
    monkeypatch.setattr(coordinate_placer_mod, "set_combo_items",
                        lambda combo, items: calls.append(list(items)))

    snapshot = [
        SimpleNamespace(role="R_SERIES", cluster="FPGA_PERIPH"),
        SimpleNamespace(role="R_SERIES", cluster="FPGA_PERIPH"),  # dedupes to the same set
    ]
    dock.refresh_known_roles(snapshot)
    first_count = len(calls)
    assert first_count == 4  # Cluster, Role, AnchorRole, AnchorCluster

    # Identical sets again — must be a no-op.
    dock.refresh_known_roles(snapshot)
    assert len(calls) == first_count

    # A brand-new role appears — repopulates again.
    dock.refresh_known_roles([
        SimpleNamespace(role="R_SERIES", cluster="FPGA_PERIPH"),
        SimpleNamespace(role="R_TERM", cluster="FPGA_PERIPH"),
    ])
    assert len(calls) == first_count + 4
