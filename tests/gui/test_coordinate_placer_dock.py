# tests/gui/test_coordinate_placer_dock.py
"""
Coordinate mode tests for the MERGED PlacerDock (2026-08-12, Group 1): the
old CoordinatePlacerDock whole-list table was removed and its single-entry
form (_CoordinatePlacementForm) merged into PlacerDock, switched via the
Source combo's "Single component" item / load_placement()'s no-cell branch.

Tests are deliberately headless AND board-mutation-free — _on_redraw()'s
real job is moving real footprints on a live board, which these tests must
never do on their own. ApplyPipeline/load_config are monkeypatched with
fakes that only check what PlacerDock PASSES them. Save, on the other hand,
is a pure file write and is exercised against real temp YAML.
"""
import yaml

import gui.docks.placer as placer_mod
from gui.docks.placer import PlacerDock
from kicadstamp.config import Config, RuntimeContext, load_coordinate_placement


def _write_yaml(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _make_dock(main_window, tmp_path):
    target_file = tmp_path / "root.yaml"
    _write_yaml(target_file, {"coordinate_placements": []})
    dock = PlacerDock(main_window)
    dock.set_target_file(target_file)
    return dock, target_file


def _new_coordinate(dock, path):
    """Switches the merged dock into coordinate mode and returns the form."""
    dock.new_coordinate_placement(path)
    return dock.coordinate_form


def _fill_cartesian(form, cluster="FPGA_PERIPH", role="R18",
                    x=10.0, y=20.0, rotation=0.0):
    form.cluster_combo.setCurrentText(cluster)
    form.role_combo.setCurrentText(role)
    form.mode_combo.setCurrentIndex(0)  # Cartesian
    form.x_edit.setText(str(x))
    form.y_edit.setText(str(y))
    form.rotation_edit.setText(str(rotation))


# ── Source-mode switching / form loading ──────────────────────────────────

def test_new_coordinate_placement_switches_mode_and_targets(main_window, tmp_path):
    dock, target_file = _make_dock(main_window, tmp_path)
    dock.new_coordinate_placement(target_file)

    assert dock.cell_mode_combo.currentIndex() == 1  # Single component
    assert dock._tabs.isTabVisible(dock._coordinate_tab_index) is True
    assert dock._tabs.isTabVisible(dock._origin_tab_index) is False
    # A fresh form is blank.
    assert dock.coordinate_form.cluster_combo.currentText() == ""
    assert dock.coordinate_form.role_combo.currentText() == ""


def test_load_placement_without_cell_switches_to_coordinate_mode(main_window, tmp_path):
    """A coordinate_placements leaf carries no cell: — load_placement() must
    detect that and load the entry into the coordinate form (2026-08-12,
    Group 1), exactly as a cell-bearing clone_placement loads the clone
    form."""
    dock, _ = _make_dock(main_window, tmp_path)
    entry = {"cluster": "FPGA_PERIPH", "role": "R18", "x_mm": 10.0, "y_mm": 20.0,
             "rotation_deg": 45.0, "anchor_point": "Origin"}

    dock.load_placement(entry)

    assert dock.cell_mode_combo.currentIndex() == 1
    form = dock.coordinate_form
    assert form.cluster_combo.currentText() == "FPGA_PERIPH"
    assert form.role_combo.currentText() == "R18"
    assert form.mode_combo.currentIndex() == 2  # anchor (anchor_point set)
    assert form.rotation_edit.text() == "45.0"
    # The anchor identity lives in the shared AnchorOriginWidget; the OFFSET
    # in the form's own Cartesian/Polar offset row.
    assert form._anchor_widget.point_edit.currentText() == "Origin"
    assert form._offset_combo.currentIndex() == 0  # Cartesian offset
    assert form._offset_x_edit.text() == "10.0"
    assert form._offset_y_edit.text() == "20.0"


def test_load_placement_cartesian_polar_and_anchor_round_trip(main_window, tmp_path):
    """The form's three position modes each load back into the SAME dict
    shape build() produces — the reverse of _build_entry_dict, so an
    already-saved entry never silently changes mode on re-save."""
    dock, _ = _make_dock(main_window, tmp_path)

    cartesian = {"cluster": "X", "role": "R1", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0}
    dock.load_placement(cartesian)
    form = dock.coordinate_form
    assert form.mode_combo.currentIndex() == 0
    entry, err = form.build()
    assert err is None and entry == {
        "cluster": "X", "role": "R1", "x_mm": 1.0, "y_mm": 2.0, "rotation_deg": 0.0}

    polar = {"cluster": "X", "role": "R2", "center_x_mm": 0.0, "center_y_mm": 0.0,
             "radius_mm": 5.0, "angle_deg": 37.0}
    dock.load_placement(polar)
    assert form.mode_combo.currentIndex() == 1
    entry, err = form.build()
    assert err is None and entry["center_x_mm"] == 0.0 and entry["radius_mm"] == 5.0
    assert entry["angle_deg"] == 37.0

    anchor = {"cluster": "X", "role": "R3", "anchor_role": "FPGA", "anchor_pad": "A17",
              "x_mm": 2.0, "y_mm": 3.0}
    dock.load_placement(anchor)
    assert form.mode_combo.currentIndex() == 2
    entry, err = form.build()
    assert err is None
    assert entry["anchor_role"] == "FPGA"
    assert entry["anchor_pad"] == "A17"
    assert entry["x_mm"] == 2.0 and entry["y_mm"] == 3.0


# ── Mode visibility ───────────────────────────────────────────────────────

def test_coordinate_mode_toggles_visible_field_groups(main_window, tmp_path):
    dock, target_file = _make_dock(main_window, tmp_path)
    form = _new_coordinate(dock, target_file)

    def cart_visible():
        return form._cartesian_row.isVisibleTo(form._cartesian_row.parentWidget())
    def polar_visible():
        return form._polar_row.isVisibleTo(form._polar_row.parentWidget())
    def anchor_visible():
        return form._anchor_widget.isVisibleTo(form._anchor_widget.parentWidget())

    form.mode_combo.setCurrentIndex(0)
    assert cart_visible() and not polar_visible() and not anchor_visible()
    form.mode_combo.setCurrentIndex(1)
    assert polar_visible() and not cart_visible() and not anchor_visible()
    form.mode_combo.setCurrentIndex(2)
    assert anchor_visible() and not cart_visible() and not polar_visible()


# ── Building the dict through the real loader ─────────────────────────────

def test_build_entry_dict_coordinate_cartesian_round_trips(main_window, tmp_path):
    dock, target_file = _make_dock(main_window, tmp_path)
    form = _new_coordinate(dock, target_file)
    _fill_cartesian(form)

    entry = dock._build_entry_dict()

    cp = load_coordinate_placement(entry)  # must validate against the real loader
    assert cp.cluster == "FPGA_PERIPH"
    assert cp.role == "R18"
    assert cp.x_mm == 10.0 and cp.y_mm == 20.0
    assert cp.rotation_deg == 0.0


def test_build_entry_dict_coordinate_polar_round_trips(main_window, tmp_path):
    dock, target_file = _make_dock(main_window, tmp_path)
    form = _new_coordinate(dock, target_file)
    form.cluster_combo.setCurrentText("FPGA_PERIPH")
    form.role_combo.setCurrentText("R19")
    form.mode_combo.setCurrentIndex(1)  # Polar (around centre)
    form.center_x_edit.setText("0.0")
    form.center_y_edit.setText("0.0")
    form.radius_edit.setText("5.0")
    form.angle_edit.setText("37.0")

    entry = dock._build_entry_dict()
    cp = load_coordinate_placement(entry)
    assert cp.center_x_mm == 0.0 and cp.radius_mm == 5.0 and cp.angle_deg == 37.0


def test_build_entry_dict_coordinate_anchor_round_trips(main_window, tmp_path):
    dock, target_file = _make_dock(main_window, tmp_path)
    form = _new_coordinate(dock, target_file)
    form.cluster_combo.setCurrentText("FPGA_PERIPH")
    form.role_combo.setCurrentText("R20")
    form.mode_combo.setCurrentIndex(2)  # Anchor
    form._anchor_widget.origin_mode_combo.setCurrentIndex(0)  # anchor (ref/role)
    form._anchor_widget.anchor_role_edit.setCurrentText("FPGA")
    form._anchor_widget.anchor_pad_edit.setText("A17")
    form._offset_combo.setCurrentIndex(0)  # Cartesian offset
    form._offset_x_edit.setText("2.0")
    form._offset_y_edit.setText("3.0")

    entry = dock._build_entry_dict()
    cp = load_coordinate_placement(entry)
    assert cp.anchor_role == "FPGA"
    assert cp.anchor_pad == "A17"
    assert cp.x_mm == 2.0 and cp.y_mm == 3.0


def test_build_entry_dict_coordinate_anchor_polar_offset_round_trips(main_window, tmp_path):
    """Anchor-relative POLAR offset (radius/angle instead of x/y) — the
    Group 2 data-loss class: load() must pass point= back through the shared
    AnchorOriginWidget (which otherwise defaults it to "" and clears the
    combo), and build() must write radius_mm/angle_deg, so the anchor is
    never dropped on re-save."""
    dock, _ = _make_dock(main_window, tmp_path)
    entry = {"cluster": "X", "role": "R21", "anchor_point": "Origin",
             "radius_mm": 5.0, "angle_deg": 37.0}
    dock.load_placement(entry)
    form = dock.coordinate_form
    assert form.mode_combo.currentIndex() == 2
    assert form._anchor_widget.point_edit.currentText() == "Origin"

    rebuilt = dock._build_entry_dict()
    cp = load_coordinate_placement(rebuilt)
    assert cp.anchor_point == "Origin"
    assert cp.radius_mm == 5.0 and cp.angle_deg == 37.0


def test_build_entry_dict_coordinate_bad_number_reports_error(main_window, tmp_path):
    dock, target_file = _make_dock(main_window, tmp_path)
    form = _new_coordinate(dock, target_file)
    _fill_cartesian(form)
    form.x_edit.setText("abc")

    entry = dock._build_entry_dict()

    assert entry is None
    assert "not a number" in dock.message_label.text()


# ── Save ──────────────────────────────────────────────────────────────────

def test_do_save_writes_coordinate_placements_section(main_window, tmp_path):
    dock, target_file = _make_dock(main_window, tmp_path)
    form = _new_coordinate(dock, target_file)
    _fill_cartesian(form, role="R18")

    dock._do_save()

    data = yaml.safe_load(target_file.read_text(encoding="utf-8"))
    entries = data["coordinate_placements"]
    assert len(entries) == 1
    cp = load_coordinate_placement(entries[0])
    assert cp.role == "R18" and cp.x_mm == 10.0 and cp.y_mm == 20.0
    assert "Wrote" in dock.message_label.text()


def test_do_save_overwrites_by_effective_name_not_duplicate(main_window, tmp_path):
    """Save twice — the second one must REPLACE the first by effective name
    (cluster/role), never append a duplicate (2026-08-12, Group 1: the
    coordinate_placements section is a named-records section like
    clone_placements/rules)."""
    dock, target_file = _make_dock(main_window, tmp_path)
    form = _new_coordinate(dock, target_file)
    _fill_cartesian(form, role="R18")

    dock._do_save()
    # Same effective name (FPGA_PERIPH/R18) but a new position.
    form.x_edit.setText("99.0")
    dock._do_save()

    data = yaml.safe_load(target_file.read_text(encoding="utf-8"))
    assert len(data["coordinate_placements"]) == 1
    assert data["coordinate_placements"][0]["x_mm"] == 99.0
    assert "Overwrote" in dock.message_label.text()


def test_save_without_target_file_shows_error(main_window, tmp_path):
    dock = PlacerDock(main_window)
    dock.new_coordinate_placement(tmp_path / "root.yaml")
    dock._placer_path = None  # simulate a dock that never got a target file

    dock._do_save()

    assert "Pick a Placer file" in dock.message_label.text()


# ── Redraw/Place (coordinate mode) ────────────────────────────────────────

def _fake_cfg_and_ctx():
    return Config(), RuntimeContext()


def test_collect_redraw_inputs_coordinate_payload(main_window, tmp_path, monkeypatch):
    dock, target_file = _make_dock(main_window, tmp_path)
    form = _new_coordinate(dock, target_file)
    _fill_cartesian(form, role="R18")

    fake_cfg, fake_ctx = _fake_cfg_and_ctx()
    monkeypatch.setattr(placer_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    payload = dock._collect_redraw_inputs()

    assert payload is not None
    assert payload["coordinate"] is True
    assert payload["name"] == "FPGA_PERIPH/R18"
    assert payload["placer_path"] == target_file
    # The form's entry replaced-by-name the in-memory cfg's coordinate_placements.
    assert [cp.role for cp in fake_cfg.coordinate_placements] == ["R18"]


def test_collect_redraw_inputs_coordinate_retired_blocked(main_window, tmp_path):
    dock, target_file = _make_dock(main_window, tmp_path)
    form = _new_coordinate(dock, target_file)
    _fill_cartesian(form)
    form.retired_checkbox.setChecked(True)

    payload = dock._collect_redraw_inputs()

    assert payload is None
    assert "retired" in dock.message_label.text().lower()


def test_run_redraw_coordinate_skips_cluster_tagging(main_window, tmp_path, monkeypatch):
    """Coordinate mode's Redraw places but does NOT tag Cluster — the moved
    component is identified by its own Cluster/Role (2026-08-12, Group 1)."""
    class _FakePipeline:
        def __init__(self, **kwargs):
            self.items = []

        def run(self):
            pass

    monkeypatch.setattr(placer_mod, "ApplyPipeline", _FakePipeline)
    dock, _ = _make_dock(main_window, tmp_path)

    result = dock._run_redraw({"placer_path": tmp_path / "root.yaml",
                               "cfg": Config(), "ctx": RuntimeContext(),
                               "name": "FPGA_PERIPH/R18", "coordinate": True})

    assert result == {"name": "FPGA_PERIPH/R18", "tagged": None}


def test_finish_redraw_coordinate_reports_simple_success(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)

    dock._finish_redraw({"name": "FPGA_PERIPH/R18", "tagged": None})

    assert "Placed" in dock.message_label.text()
    assert "tagged" not in dock.message_label.text()


# ── DetailDock title helper ───────────────────────────────────────────────

def test_current_entity_name_in_coordinate_mode(main_window, tmp_path):
    dock, target_file = _make_dock(main_window, tmp_path)
    form = _new_coordinate(dock, target_file)
    form.cluster_combo.setCurrentText("FPGA_PERIPH")
    form.role_combo.setCurrentText("R18")

    assert dock.current_entity_name == "FPGA_PERIPH"

    form.name_edit.setText("my_cap")
    assert dock.current_entity_name == "my_cap"
